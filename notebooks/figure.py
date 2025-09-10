import matplotlib.pyplot as plt


def plot_data(im, name=None):


    if im is not None:
        plt.close("all")
        fig , axs = plt.subplots(1, 2, figsize=(12, 6), layout = 'constrained')
        
        pcm = axs[0].imshow(im, cmap='terrain')
        fig.colorbar(pcm, ax=axs[0])

        pcm = axs[1].imshow(im, cmap='jet')
        fig.colorbar(pcm, ax=axs[1])
        

        plt.figure()
        plt.hist(im.ravel()[::10],100)


    if name is not None:
        fig.set_title(name)
        plt.suptitle(name)

