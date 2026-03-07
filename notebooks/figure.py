import matplotlib.pyplot as plt
import numpy as np 

def plot_data(im, name=None, bbox = None, close=False):

    im = im[::-1].copy()

    if im is None:
        return
    
    if close:
        plt.close("all")
    fig , axs = plt.subplots(1, 2, figsize=(12, 6), 
                    layout = 'tight', 
                    sharex=True, sharey=True)
    
    pcm = axs[0].imshow(im, cmap='terrain')
    fig.colorbar(pcm, ax=axs[0] )

    pcm = axs[1].imshow(im, cmap='rainbow')
    fig.colorbar(pcm, ax=axs[1])

    axs[0].grid(True)
    axs[1].grid(True)
    
    fig , axs = plt.subplots(1,2, figsize=(12, 6), 
                    layout = 'tight')
        
    pcm = axs[0].imshow(im, cmap='gray', extent=bbox)
    pcm = axs[1].imshow(im, cmap='gray')

    axs[0].grid(True, color="red")



    a = -1
    x_range = np.linspace(0, im.shape[0]-1, 10).astype(int)
    y_range = np.linspace(0, im.shape[1]-1, 10).astype(int)


    x_range = np.arange(0,im.shape[0], 50)
    y_range = np.arange(0,im.shape[1], 50)

    ###############################
    if 0:
        for i in x_range:
            y = a*im[i]+i
            axs[0].axhline(i)
            #axs[0].plot(y)

        for i in y_range:   
            y = a*im[:,i]+i
            axs[0].axvline(i)
            #axs[0].plot(y,range(len(im[:,i])))

    ###########################
    for i in x_range:
        y = im[i]
        y = np.concatenate([[0],y,[0]])
        y = a*y+i
        
        axs[1].axhline(i)
        axs[1].fill(y)

    for i in y_range:   
        y = a*im[:,i]+i
        axs[1].axvline(i)
        axs[1].plot(y,range(len(im[:,i])))
    
    plt.figure()
    plt.hist(im.ravel()[::10],100)

    if name is not None:
        fig.set_title(name)
        plt.suptitle(name)

    print(im.shape)

